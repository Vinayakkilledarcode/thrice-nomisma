import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[] arr1= new int[n];
        int[] arr2 = new int[n];
        
        for(int i=0;i<n;i++){
            arr1[i]=sc.nextInt();
        }
        for(int i=0;i<n;i++){
            arr2[i]=sc.nextInt();
        }
        
        Set<Integer> set = new HashSet<>();
        
        for(int x: arr1){
            set.add(x);
        }
        for(int x:arr2){
           if(set.contains(x)){
               System.out.print(x);
           }
        }
    }
}