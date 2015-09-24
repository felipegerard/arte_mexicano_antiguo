import luigi
import os
import pickle
from scipy import misc
from joblib import Parallel, delayed  
import multiprocessing
import csv
import re
import shutil
import skimage.io as io
from skimage.color import rgb2gray
from skimage.filters import gaussian_filter
import subprocess
from sklearn.decomposition import TruncatedSVD
from scipy.sparse import csr_matrix
from sklearn.cluster import KMeans

def image_vec(pagina):
    pagina = misc.imread(pagina)
    pagina_2 = rgb2gray(pagina)
    return pagina_2.reshape(1,60000)

def is_jpg(filename):
    data = open(filename,'rb').read(11)
    if data[:4] != '\xff\xd8\xff\xe0': return False
    if data[6:] != 'JFIF\0': return False
    return True    

class CarpetaLibro(luigi.ExternalTask):
	"""
	Todo empieza dando de alta la carpeta donde estan 
	todos los libros.
	"""
	input_dir = luigi.Parameter()

	def output(self):
		"""
		Solo va a regresar la existencia de la carpeta
		de la carpeta con todos los libros
		"""

		#El directorio donde viven todos los libros
		return luigi.LocalTarget(self.input_dir)
		

class IdentificarImagenes(luigi.Task):
	"""
	Se a toma el listado de los jpgs y se analiza
	si en esta pagina puede ver una pintura
	"""
	input_dir = luigi.Parameter()
	carpeta_lib = luigi.Parameter()
	output_dir = luigi.Parameter(default = 'Salidas')
	varianza = luigi.FloatParameter(default=0.05)
	
	def requires(self):
		return CarpetaLibro(os.path.join(self.input_dir, self.carpeta_lib))

	def output(self):		
		return luigi.LocalTarget(os.path.join(self.output_dir,self.carpeta_lib + ".csv"))

	def run(self):
		if not os.path.exists(self.output_dir):
			os.makedirs(self.output_dir)
			print 'USER INFO: Creando carpeta de listas de archivos con imagenes en ' + self.output_dir
		
		resultado = []

		try:
			list_jpgs = os.listdir(os.path.join(self.input().path, "jpg"))
					
			for jpg in list_jpgs:
				try:
					#Creamos la ruta para cada jpg
					ruta_jpg = os.path.join(self.input().path,"jpg",jpg)
					#abrimos cada jpg y checamos su varianza, si es alta la guardamos
					pagina = io.imread(ruta_jpg)
					pagina = rgb2gray(pagina)
					pagina = gaussian_filter(pagina,sigma=2) #Un filtro que me de un promedio de la imagen
					if pagina.var() > self.varianza:
						resultado.append(ruta_jpg)
				except:
					pass
		except:
			pass		
		
		resultado = '\n'.join(resultado)
		with self.output().open("w") as f:
			f.write(resultado)

class CopiarImagenReducir(luigi.Task):
	"""
	Este proceso va a hacer una copia de los jpgs que se detecto
	con imagenes a otra carpeta y luego los va a reducir de tamano
	a 200x300
	"""

	input_dir = luigi.Parameter()
	carpeta_lib = luigi.Parameter()
	output_dir = luigi.Parameter(default = 'Salidas')
	varianza = luigi.FloatParameter(default=0.05)
	output_dir_imag = luigi.Parameter(default = 'Imagenes')

	def requires(self):
		return IdentificarImagenes(self.input_dir,self.carpeta_lib, self.output_dir, self.varianza)
	
	def output(self):
		directorio_salida = os.path.join(self.output_dir, self.output_dir_imag)
		imagenes = []
		try:		
			with open(self.input().path, 'rb') as csvfile:
				leer = csv.reader(csvfile, delimiter=' ', quotechar='\n')
				for row in leer:
					imagenes.append(row)

			if len(imagenes) > 0:
				return [luigi.LocalTarget(os.path.join(directorio_salida,re.split("/",doc[0])[-1])) for doc in imagenes]
			else:
				return luigi.LocalTarget(self.input().path)
		except:
			luigi.LocalTarget(os.path.join(directorio_salida,"dummy.jpg"))

	def run(self):
		imagenes = []
		with open(self.input().path, 'rb') as csvfile:
			leer = csv.reader(csvfile, delimiter=' ', quotechar='\n')
			for row in leer:
				imagenes.append(row)

		directorio_salida = os.path.join(self.output_dir, self.output_dir_imag)
		if not os.path.exists(directorio_salida):
			os.makedirs(os.path.join(self.output_dir, self.output_dir_imag))
			print 'USER INFO: Creando carpeta de listas de archivos con imagenes en ' + self.output_dir_imag

		for imagen in imagenes:
			shutil.copy2(imagen[0], directorio_salida)
			subprocess.call('bash reducir_imagenes.sh' + " " + os.path.join(directorio_salida,re.split("/",imagen[0])[-1]), shell = True)

class SacarClusters(luigi.Task):
	"""
	En este proceso voy a hacer SVD y quedarme con un numero de
	componentes para despues hacer K-means y sacar clusters
	"""

	input_dir = luigi.Parameter()
	output_dir = luigi.Parameter(default = 'Salidas')
	varianza = luigi.FloatParameter(default=0.05)
	output_dir_imag = luigi.Parameter(default = 'Imagenes')
	num_clusters = luigi.IntParameter(default = 5)
	components = luigi.IntParameter(default = 8)

	def requires(self):
		return [CopiarImagenReducir(self.input_dir, x ,self.output_dir,
			self.varianza,self.output_dir_imag) for x in os.listdir(self.input_dir)]

	def output(self):
		return luigi.LocalTarget(os.path.join(self.input_dir,self.output_dir,"clusters.csv"))

	def run(self):
		print "colectando imagenes" 
		imagenes = []
		for imagen in self.input():
			if not os.path.isdir(imagen):
				imagenes.append(imagen.path)
			
		print "conviriendo a vector"	
		mat_pin = [image_vec(pagina)[0] for pagina in imagenes]
		#mat_pin = np.array(mat_pin)/256
		#mat_pin = mat_pin.round()
		#mat_pin = csr_matrix(mat_pin)

		print "Sacando SVD"
		svd = TruncatedSVD(n_components=self.components, random_state=42)

		X = svd.fit_transform(mat_pin) 

		print(svd.explained_variance_ratio_)
		print(svd.explained_variance_ratio_.sum())

		print "Sacando KMeans"
		km = KMeans(n_clusters=self.num_clusters, init='k-means++', max_iter=100, n_init=1)

		km.fit(X)

		print "Guardando a un directorio de cluster"
		for i in range(self.num_clusters):
			newpath = os.path.join(self.output_dir,self.output_dir_imag,"cluster_") + str(i)
			if not os.path.exists(newpath): os.makedirs(newpath)

		imag = os.listdir(os.path.join(self.output_dir,self.output_dir_imag))
		clusters = []
		for i in range(len(imagenes)):
		    os.rename(imagenes[i], os.path.join(self.output_dir,self.output_dir_imag,"cluster_") + str(km.labels_[i]) + "/" + str(imag[i]))
		    clusters.append([imagenes[i],km.labels_[i]])
		
		with self.output().open("w") as output:
		    writer = csv.writer(output, lineterminator='\n')
		    for val in clusters:
		        writer.writerow(val)

if __name__ == '__main__':
	luigi.run()